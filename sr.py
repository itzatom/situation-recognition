import torch
import json
import os

import model
from utils import imsitu_encoder, imsitu_loader, imsitu_scorer, utils

def train(model, train_loader, dev_loader, optimizer, scheduler, max_epoch, model_dir, encoder, clip_norm, model_name, model_saving_name, eval_frequency=2000, verbose=False):
  model.train()
  train_loss = 0
  total_steps = 0
  print_flag = False
  dev_score_list = []

  print("Let's use", torch.cuda.device_count(), "GPUs!")
  model = torch.nn.DataParallel(model)

  top1 = imsitu_scorer.imsitu_scorer(encoder, 1, 3)
  top5 = imsitu_scorer.imsitu_scorer(encoder, 5, 3)

  for epoch in range(max_epoch):
    print('Starting epoch: ', epoch, ', current learning rate: ', scheduler.get_lr())
    
    for i, (_, img, verb, labels) in enumerate(train_loader):
      total_steps += 1

      img = torch.autograd.Variable(img.cuda())
      verb = torch.autograd.Variable(verb.cuda())
      labels = torch.autograd.Variable(labels.cuda())
        
      #if verbose flag is set and iterated 40 images then print
      if total_steps % 40 == 0 and verbose:
        print_flag = True

      if print_flag is True:
        print('Predicting roles in frame')
        
        role_predict = model(img, verb)

      if print_flag is True:
        print('Calculating loss')
        
      loss = model.module.calculate_loss(verb, role_predict, labels)

      if print_flag is True:
        print('Backpropragating through time')
        
      loss.backward()

      torch.nn.utils.clip_grad_norm_(model.parameters(), clip_norm)

      optimizer.step()
      optimizer.zero_grad()

      train_loss += loss.item()

      top1.add_point_noun(verb, role_predict, labels)
      top5.add_point_noun(verb, role_predict, labels)


      if print_flag is True:
        top1_a = top1.get_average_results_nouns()
        top5_a = top5.get_average_results_nouns()
        print("Total_steps: {}, elements n°: {}, {} , {}, loss = {:.2f}, avg loss = {:.2f}"
          .format(total_steps-1, i,
          utils.format_dict(top1_a, "{:.2f}", "1-"),
          utils.format_dict(top5_a,"{:.2f}","5-"),
          loss.item(), train_loss/((total_steps-1)%eval_frequency)
          )
        )


      if total_steps % eval_frequency == 0:
        top1, top5, val_loss = eval(model, dev_loader, encoder)
        model.train()

        top1_avg = top1.get_average_results_nouns()
        top5_avg = top5.get_average_results_nouns()

        avg_score = top1_avg["verb"] + top1_avg["value"] + top1_avg["value-all"] + top5_avg["verb"] + \
                    top5_avg["value"] + top5_avg["value-all"] + top5_avg["value*"] + top5_avg["value-all*"]
        avg_score /= 8

        print ('Dev {} average :{:.2f} {} {}'.format(total_steps-1, avg_score*100,
                                                      utils.format_dict(top1_avg,'{:.2f}', '1-'),
                                                      utils.format_dict(top5_avg, '{:.2f}', '5-')))
        dev_score_list.append(avg_score)
        max_score = max(dev_score_list)

        if max_score == dev_score_list[-1]:
          torch.save(model.state_dict(), model_dir + "/{}_{}.model".format( model_name, model_saving_name))
          print ('New best model saved! {0}'.format(max_score))

        print('current train loss', train_loss)
        train_loss = 0
        top1 = imsitu_scorer.imsitu_scorer(encoder, 1, 3)
        top5 = imsitu_scorer.imsitu_scorer(encoder, 5, 3)

      if print_flag is True:
        print_flag = False
      
    #del role_predict, loss, img, verb, labels
    
  scheduler.step()

def eval(model, dev_loader, encoder, write_to_file = False):
  model.eval()

  print ('evaluating model...')
  top1 = imsitu_scorer.imsitu_scorer(encoder, 1, 3, write_to_file)
  top5 = imsitu_scorer.imsitu_scorer(encoder, 5, 3)
  with torch.no_grad():

    for i, (img_id, img, verb, labels) in enumerate(dev_loader):

      img = torch.autograd.Variable(img.cuda())
      verb = torch.autograd.Variable(verb.cuda())
      labels = torch.autograd.Variable(labels.cuda())

      role_predict = model(img, verb)

      if write_to_file:
        top1.add_point_noun_log(img_id, verb, role_predict, labels)
        top5.add_point_noun_log(img_id, verb, role_predict, labels)
      else:
        top1.add_point_noun(verb, role_predict, labels)
        top5.add_point_noun(verb, role_predict, labels)

      del role_predict, img, verb, labels

  return top1, top5, 0

if __name__ == "__main__":
  import argparse
  parser = argparse.ArgumentParser(description="imsitu VSRL. Training, evaluation and prediction.")
  parser.add_argument('--output_dir', type=str, default='./trained_models', help='Location to output the model')
  parser.add_argument('--resume_training', action='store_true', help='Resume training from the model [resume_model]')
  parser.add_argument('--resume_model', type=str, default='', help='The model we resume')
  parser.add_argument('--evaluate', action='store_true', help='Only use the testing mode')
  parser.add_argument('--test', action='store_true', help='Only use the testing mode')
  parser.add_argument('--dataset_folder', type=str, default='./imSitu', help='Location of annotations')
  parser.add_argument('--imgset_dir', type=str, default='./resized_256', help='Location of original images')
  parser.add_argument('--train_file', default="train.json", type=str, help='trainfile name')
  parser.add_argument('--dev_file', default="dev.json", type=str, help='dev file name')
  parser.add_argument('--test_file', default="test.json", type=str, help='test file name')
  parser.add_argument('--model_saving_name', type=str, help='saving name of the outpul model')
  parser.add_argument('--verbose', action='store_true', help='set verbose mode')

  parser.add_argument('--epochs', type=int, default=500)
  parser.add_argument('--seed', type=int, default=1111, help='random seed')
  parser.add_argument('--clip_norm', type=float, default=0.25)
  parser.add_argument('--num_workers', type=int, default=3)

  args = parser.parse_args()

  n_epoch = args.epochs
  batch_size = 64 * torch.cuda.device_count()
  clip_norm = args.clip_norm
  n_worker = args.num_workers

  dataset_folder = args.dataset_folder
  imgset_folder = args.imgset_dir

  train_set = json.load(open(dataset_folder + '/' + args.train_file))

  encoder = imsitu_encoder.imsitu_encoder(train_set)

  model = model.build_ggnn_baseline(encoder.get_num_roles(), encoder.get_num_verbs(), encoder.get_num_labels(), encoder)
  
  train_set = imsitu_loader.imsitu_loader(imgset_folder, train_set, encoder,'train', encoder.train_transform)
  train_loader = torch.utils.data.DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=n_worker)

  dev_set = json.load(open(dataset_folder + '/' + args.dev_file))
  dev_set = imsitu_loader.imsitu_loader(imgset_folder, dev_set, encoder, 'val', encoder.dev_transform)
  dev_loader = torch.utils.data.DataLoader(dev_set, batch_size=batch_size, shuffle=True, num_workers=n_worker)

  test_set = json.load(open(dataset_folder + '/' + args.test_file))
  test_set = imsitu_loader.imsitu_loader(imgset_folder, test_set, encoder, 'test', encoder.dev_transform)
  test_loader = torch.utils.data.DataLoader(test_set, batch_size=batch_size, shuffle=True, num_workers=n_worker)

  if not os.path.exists(args.output_dir):
    os.mkdir(args.output_dir)

  torch.manual_seed(args.seed)
    
  model.cuda()
  torch.cuda.manual_seed(args.seed)
  torch.backends.cudnn.benchmark = True

  if args.resume_training:
    print('Resume training from: {}'.format(args.resume_model))
    args.train_all = True
    if len(args.resume_model) == 0:
      raise Exception('[pretrained module] not specified')
    utils.load_net(args.resume_model, [model])
    optimizer = torch.optim.RMSprop(model.parameters(), lr=1e-3)
    model_name = 'resume_all'

  else:
    print('Training from the scratch.')
    model_name = 'train_full'
    utils.set_trainable(model, True)
    optimizer = torch.optim.RMSprop([
        {'params': model.convnet.parameters(), 'lr': 5e-5},
        {'params': model.role_emb.parameters()},
        {'params': model.verb_emb.parameters()},
        {'params': model.ggnn.parameters()},
        {'params': model.classifier.parameters()}
    ], lr=1e-3)

  scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10 ,gamma=0.85)
    
  if args.evaluate:
    top1, top5, val_loss = eval(model, dev_loader, encoder)

    top1_avg = top1.get_average_results_nouns()
    top5_avg = top5.get_average_results_nouns()

    avg_score = top1_avg["verb"] + top1_avg["value"] + top1_avg["value-all"] + top5_avg["verb"] + \
                top5_avg["value"] + top5_avg["value-all"] + top5_avg["value*"] + top5_avg["value-all*"]
    avg_score /= 8

    print('Dev average :{:.2f} {} {}'
          .format( avg_score*100,
          utils.format_dict(top1_avg,'{:.2f}', '1-'),
          utils.format_dict(top5_avg, '{:.2f}', '5-')))


  elif args.test:
    top1, top5, val_loss = eval(model, test_loader, encoder)

    top1_avg = top1.get_average_results_nouns()
    top5_avg = top5.get_average_results_nouns()

    avg_score = top1_avg["verb"] + top1_avg["value"] + top1_avg["value-all"] + top5_avg["verb"] + \
                top5_avg["value"] + top5_avg["value-all"] + top5_avg["value*"] + top5_avg["value-all*"]
    avg_score /= 8

    print ('Test average :{:.2f} {} {}'
            .format( avg_score*100,
            utils.format_dict(top1_avg,'{:.2f}', '1-'),
            utils.format_dict(top5_avg, '{:.2f}', '5-')))


  else:
    print('Model training started!')
    train(model, train_loader, dev_loader, optimizer, scheduler, n_epoch, args.output_dir, encoder, clip_norm, model_name, args.model_saving_name,
    verbose=args.verbose)















